from __future__ import annotations

import logging
import time
from asyncio import Semaphore

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.resilience.memory_monitor import is_memory_pressure, is_memory_warning
from app.core.resilience.overload import (
    deny_websocket_with_http_response,
    is_proxy_path,
    local_overload_error,
    local_unavailable_error,
    merge_retry_after_headers,
    send_json_http_response,
)
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)

_MEMORY_WARNING_LOG_INTERVAL = 30.0
_last_memory_warning_log: float = 0.0


class BulkheadSemaphore:
    def __init__(
        self,
        proxy_limit: int | None = None,
        dashboard_limit: int = 50,
        background_limit: int = 10,
        *,
        proxy_http_limit: int | None = None,
        proxy_websocket_limit: int | None = None,
        proxy_compact_limit: int | None = None,
    ) -> None:
        resolved_proxy_limit = 200 if proxy_limit is None else proxy_limit
        resolved_proxy_http_limit = resolved_proxy_limit if proxy_http_limit is None else proxy_http_limit
        resolved_proxy_websocket_limit = (
            resolved_proxy_limit if proxy_websocket_limit is None else proxy_websocket_limit
        )
        if proxy_compact_limit is None:
            resolved_proxy_compact_limit = 0 if resolved_proxy_http_limit <= 0 else min(resolved_proxy_http_limit, 16)
        else:
            resolved_proxy_compact_limit = proxy_compact_limit
        self._proxy_http = Semaphore(resolved_proxy_http_limit) if resolved_proxy_http_limit > 0 else None
        self._proxy_websocket = (
            Semaphore(resolved_proxy_websocket_limit) if resolved_proxy_websocket_limit > 0 else None
        )
        self._proxy_compact = Semaphore(resolved_proxy_compact_limit) if resolved_proxy_compact_limit > 0 else None
        self._dashboard = Semaphore(dashboard_limit) if dashboard_limit > 0 else None
        self._background = Semaphore(background_limit) if background_limit > 0 else None

    def get_semaphore(self, scope_type: str, path: str) -> tuple[str, Semaphore | None]:
        if scope_type == "websocket" and is_proxy_path(path):
            return "proxy_websocket", self._proxy_websocket
        if scope_type == "http" and _is_compact_path(path):
            return "proxy_compact", self._proxy_compact
        if scope_type == "http" and is_proxy_path(path):
            return "proxy_http", self._proxy_http
        return "dashboard", self._dashboard


class BulkheadMiddleware:
    def __init__(self, app: ASGIApp, *, bulkhead: BulkheadSemaphore) -> None:
        self.app = app
        self._bulkhead = bulkhead

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        if path.startswith("/health"):
            await self.app(scope, receive, send)
            return

        if is_memory_warning():
            global _last_memory_warning_log
            now = time.monotonic()
            if now - _last_memory_warning_log >= _MEMORY_WARNING_LOG_INTERVAL:
                _last_memory_warning_log = now
                logger.warning("Memory warning threshold exceeded")

        if is_memory_pressure():
            message = "codex-lb is temporarily unavailable due to local memory pressure"
            await self._log_rejection(
                path=path,
                scope_type=scope["type"],
                lane="memory",
                status_code=503,
                message=message,
                available=None,
            )
            if scope["type"] == "websocket":
                await deny_websocket_with_http_response(
                    receive,
                    send,
                    status_code=503,
                    payload=local_unavailable_error(message) if is_proxy_path(path) else {"detail": message},
                    headers=merge_retry_after_headers(),
                )
                return
            await send_json_http_response(
                send,
                status_code=503,
                payload=local_unavailable_error(message) if is_proxy_path(path) else {"detail": message},
                headers=merge_retry_after_headers(),
            )
            return

        lane, sem = self._bulkhead.get_semaphore(scope["type"], path)
        if sem is None:
            await self.app(scope, receive, send)
            return

        if sem.locked():
            message = f"codex-lb is temporarily overloaded in the {lane} lane"
            await self._log_rejection(
                path=path,
                scope_type=scope["type"],
                lane=lane,
                status_code=429,
                message=message,
                available=0,
            )
            if scope["type"] == "websocket":
                await deny_websocket_with_http_response(
                    receive,
                    send,
                    status_code=429,
                    payload=local_overload_error(message) if is_proxy_path(path) else {"detail": message},
                    headers=merge_retry_after_headers(),
                )
                return
            await send_json_http_response(
                send,
                status_code=429,
                payload=local_overload_error(message) if is_proxy_path(path) else {"detail": message},
                headers=merge_retry_after_headers(),
            )
            return

        await sem.acquire()

        try:
            await self.app(scope, receive, send)
        finally:
            sem.release()

    async def _log_rejection(
        self,
        *,
        path: str,
        scope_type: str,
        lane: str,
        status_code: int,
        message: str,
        available: int | None,
    ) -> None:
        logger.warning(
            "proxy_admission_rejected request_id=%s scope=%s path=%s lane=%s status=%s available=%s message=%s",
            get_request_id(),
            scope_type,
            path,
            lane,
            status_code,
            available,
            message,
        )


_bulkhead: BulkheadSemaphore | None = None


def _is_compact_path(path: str) -> bool:
    return path.endswith("/responses/compact")


def get_bulkhead(
    *,
    proxy_http_limit: int = 200,
    proxy_websocket_limit: int = 200,
    proxy_compact_limit: int | None = None,
    dashboard_limit: int = 50,
) -> BulkheadSemaphore:
    global _bulkhead
    if _bulkhead is None:
        _bulkhead = BulkheadSemaphore(
            proxy_http_limit=proxy_http_limit,
            proxy_websocket_limit=proxy_websocket_limit,
            proxy_compact_limit=proxy_compact_limit,
            dashboard_limit=dashboard_limit,
        )
    return _bulkhead


__all__ = ["BulkheadMiddleware", "BulkheadSemaphore", "get_bulkhead"]
