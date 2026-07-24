from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.core.clients.proxy import ProxyResponseError, apply_codex_installation_headers
from app.core.clients.proxy_websocket import (
    UpstreamResponsesWebSocket,
    UpstreamWebSocketTransportError,
)
from app.core.clients.proxy_websocket import (
    connect_live_websocket as core_connect_live_websocket,
)
from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, StickySessionKind
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._service.support import _request_log_client_fields
from app.modules.proxy.helpers import _header_account_id
from app.modules.proxy.load_balancer import AccountLease, AccountSelection

logger = logging.getLogger("app.modules.proxy.service")

_REALTIME_CALL_AFFINITY_PREFIX = "\ncodex_live_call:"
_REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS = 2 * 60 * 60
_REALTIME_CALL_CLEANUP_INTERVAL_SECONDS = 5 * 60
_REALTIME_CALL_CLEANUP_BATCH_SIZE = 250
_REALTIME_CALL_ID_MAX_LENGTH = 256
_REALTIME_CALL_ID_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._~-")
_REALTIME_CALL_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)
_REQUEST_TRANSPORT_WEBSOCKET = "websocket"

_realtime_call_cleanup_lock = asyncio.Lock()
_realtime_call_cleanup_last_monotonic = 0.0


class _RealtimeLiveServiceProtocol(Protocol):
    _encryptor: Any
    _load_balancer: Any
    _repo_factory: Any

    async def _select_account_with_budget_compatible(self, deadline: float, **kwargs: object) -> AccountSelection: ...

    async def _resolve_upstream_route_for_account(
        self,
        account: Account,
        *,
        operation: str,
    ) -> ResolvedUpstreamRoute | None: ...

    async def _write_request_log(self, **kwargs: Any) -> None: ...


def _service_connect_live_websocket() -> Callable[..., Awaitable[UpstreamResponsesWebSocket]]:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        return cast(
            Callable[..., Awaitable[UpstreamResponsesWebSocket]],
            getattr(service_module, "connect_live_websocket", core_connect_live_websocket),
        )
    return core_connect_live_websocket


def normalize_realtime_call_id(value: str) -> str | None:
    normalized = value.strip()
    if not normalized or len(normalized) > _REALTIME_CALL_ID_MAX_LENGTH:
        return None
    rtc_shaped = (
        normalized.startswith("rtc_")
        and len(normalized) > len("rtc_")
        and all(character in _REALTIME_CALL_ID_CHARACTERS for character in normalized)
    )
    if not rtc_shaped and _REALTIME_CALL_UUID_PATTERN.fullmatch(normalized) is None:
        return None
    return normalized


def realtime_call_id_from_location(headers: Mapping[str, str]) -> str | None:
    location = next((value for key, value in headers.items() if key.lower() == "location"), None)
    if not location:
        return None
    segments = [segment for segment in urlparse(location).path.split("/") if segment]
    if len(segments) >= 2 and segments[-2] == "live":
        return normalize_realtime_call_id(segments[-1])
    if len(segments) >= 3 and segments[-3:-1] == ["realtime", "calls"]:
        return normalize_realtime_call_id(segments[-1])
    return None


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


async def _relay_downstream_to_upstream(
    websocket: WebSocket,
    upstream: UpstreamResponsesWebSocket,
    *,
    max_message_bytes: int,
) -> None:
    while True:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            await upstream.close(
                code=_valid_close_code(message.get("code"), default=1000),
                reason=_bounded_close_reason(message.get("reason")),
            )
            return
        text = message.get("text")
        if isinstance(text, str):
            if len(text.encode("utf-8")) > max_message_bytes:
                await upstream.close(code=1009)
                await _safe_close_downstream(websocket, code=1009)
                return
            await upstream.send_text(text)
            continue
        data = message.get("bytes")
        if isinstance(data, bytes):
            if len(data) > max_message_bytes:
                await upstream.close(code=1009)
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
    upstream: UpstreamResponsesWebSocket,
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
    upstream: UpstreamResponsesWebSocket,
    *,
    max_message_bytes: int,
) -> None:
    tasks = {
        asyncio.create_task(
            _relay_downstream_to_upstream(
                websocket,
                upstream,
                max_message_bytes=max_message_bytes,
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
        if owner_account_id is None:
            raise ProxyResponseError(
                404,
                openai_error("realtime_call_not_found", "Realtime call binding not found or expired"),
            )

        request_id = get_request_id() or ensure_request_id(None)
        start = time.monotonic()
        settings = get_settings()
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

        upstream: UpstreamResponsesWebSocket | None = None
        log_status = "error"
        log_error_code: str | None = None
        log_error_message: str | None = None
        useragent, useragent_group, conversation_id = _request_log_client_fields(headers)
        route: ResolvedUpstreamRoute | None = None
        try:
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
                getattr(account, "codex_installation_id", None),
            )
            route = await proxy._resolve_upstream_route_for_account(
                account,
                operation="realtime_live_websocket",
            )
            upstream = await _service_connect_live_websocket()(
                normalized_call_id,
                forwarded_headers,
                access_token,
                _header_account_id(account.chatgpt_account_id),
                route=route,
                allow_direct_egress=route is None,
                query_params=(list(query_params.items()) if isinstance(query_params, Mapping) else list(query_params)),
            )
            await websocket.accept()
            await _relay_live_websocket(
                websocket,
                upstream,
                max_message_bytes=settings.max_sse_event_bytes,
            )
            log_status = "success"
        except WebSocketDisconnect:
            log_status = "success"
        except asyncio.CancelledError:
            log_error_code = "cancelled"
            log_error_message = "Realtime live websocket cancelled"
            raise
        except ProxyResponseError:
            log_error_code = "upstream_error"
            log_error_message = "Realtime live websocket handshake failed"
            if websocket.application_state == WebSocketState.CONNECTED:
                await _safe_close_downstream(websocket, code=1011)
                return
            raise
        except UpstreamWebSocketTransportError as exc:
            log_error_code = exc.error_code
            log_error_message = "Realtime live websocket transport failed"
            await _safe_close_downstream(websocket, code=1011)
        except Exception as exc:
            log_error_code = "realtime_live_unavailable"
            log_error_message = "Realtime live websocket failed"
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
                if upstream is not None:
                    try:
                        await asyncio.wait_for(
                            upstream.close(),
                            timeout=max(1.0, settings.upstream_connect_timeout_seconds),
                        )
                    except Exception:
                        logger.warning("Failed to close realtime live upstream websocket")
                        log_status = "error"
                        if log_error_code is None:
                            log_error_code = "upstream_close_failed"
                            log_error_message = "Realtime live upstream close failed"
            finally:
                await proxy._load_balancer.release_account_lease(account_lease)
            try:
                await proxy._write_request_log(
                    account_id=account.id,
                    api_key=api_key,
                    request_id=request_id,
                    model=None,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    status=log_status,
                    request_kind="realtime_live",
                    error_code=log_error_code,
                    error_message=log_error_message,
                    transport=_REQUEST_TRANSPORT_WEBSOCKET,
                    useragent=useragent,
                    useragent_group=useragent_group,
                    client_ip=client_ip,
                    conversation_id=conversation_id,
                    upstream_proxy_route_mode=(
                        getattr(upstream, "upstream_proxy_route_mode", None)
                        if upstream is not None
                        else (route.mode if route is not None else None)
                    ),
                    upstream_proxy_pool_id=(
                        getattr(upstream, "upstream_proxy_pool_id", None)
                        if upstream is not None
                        else (route.pool_id if route is not None else None)
                    ),
                    upstream_proxy_endpoint_id=(
                        getattr(upstream, "upstream_proxy_endpoint_id", None)
                        if upstream is not None
                        else (route.endpoint_id if route is not None else None)
                    ),
                    upstream_proxy_fallback_used=(
                        getattr(upstream, "upstream_proxy_fallback_used", None) if upstream is not None else None
                    ),
                )
            except Exception:
                logger.exception("Failed to write realtime live websocket request log")
